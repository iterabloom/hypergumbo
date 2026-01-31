"""Tests for the Hack language analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import hack as hack_module
from hypergumbo_lang_extended1.hack import (
    HackAnalysisResult,
    analyze_hack,
    find_hack_files,
    is_hack_tree_sitter_available,
)


def make_hack_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a Hack file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindHackFiles:
    """Tests for find_hack_files function."""

    def test_finds_hack_files(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "main.hack", "<?hh\necho 'hello';")
        make_hack_file(tmp_path, "helper.hh", "<?hh\nfunction h() {}")
        files = find_hack_files(tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"main.hack", "helper.hh"}

    def test_finds_php_files_with_hh_header(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.php", "<?hh\nclass Test {}")
        make_hack_file(tmp_path, "regular.php", "<?php\necho 'php';")
        files = find_hack_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "test.php"

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_hack_files(tmp_path)
        assert files == []


class TestIsHackTreeSitterAvailable:
    """Tests for is_hack_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_hack_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(hack_module, "is_hack_tree_sitter_available", return_value=False):
            assert hack_module.is_hack_tree_sitter_available() is False


class TestAnalyzeHack:
    """Tests for analyze_hack function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", "<?hh\necho 'test';")
        with patch.object(hack_module, "is_hack_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Hack analysis skipped"):
                result = hack_module.analyze_hack(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_classes(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "User.hack", """<?hh
class User {
  public function getName(): string {
    return "test";
  }
}
""")
        result = analyze_hack(tmp_path)
        assert not result.skipped
        cls = next((s for s in result.symbols if s.name == "User"), None)
        assert cls is not None
        assert cls.kind == "class"
        assert cls.language == "hack"

    def test_extracts_interfaces(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "IUser.hack", """<?hh
interface IUser {
  public function getName(): string;
}
""")
        result = analyze_hack(tmp_path)
        iface = next((s for s in result.symbols if s.name == "IUser"), None)
        assert iface is not None
        assert iface.kind == "interface"

    def test_extracts_traits(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Loggable.hack", """<?hh
trait Loggable {
  public function log(string $msg): void {
    echo $msg;
  }
}
""")
        result = analyze_hack(tmp_path)
        trait = next((s for s in result.symbols if s.name == "Loggable"), None)
        assert trait is not None
        assert trait.kind == "trait"

    def test_extracts_functions(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "helpers.hack", """<?hh
function add(int $a, int $b): int {
  return $a + $b;
}
""")
        result = analyze_hack(tmp_path)
        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.kind == "function"
        assert func.meta["param_count"] == 2
        assert "int" in func.signature

    def test_extracts_methods(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Test.hack", """<?hh
class Test {
  public function greet(): string {
    return "Hello";
  }

  private static function helper(): void {}
}
""")
        result = analyze_hack(tmp_path)
        method = next((s for s in result.symbols if "greet" in s.name), None)
        assert method is not None
        assert method.kind == "method"
        assert method.meta["visibility"] == "public"
        assert method.meta["class"] == "Test"

        static_method = next((s for s in result.symbols if "helper" in s.name), None)
        assert static_method is not None
        assert static_method.meta["static"] is True
        assert static_method.meta["visibility"] == "private"

    def test_extracts_namespaced_symbols(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Controller.hack", r"""<?hh
namespace MyApp\Controllers;

class UserController {
  public function index(): void {}
}
""")
        result = analyze_hack(tmp_path)
        ns = next((s for s in result.symbols if s.kind == "namespace"), None)
        assert ns is not None
        assert "Controllers" in ns.name

        cls = next((s for s in result.symbols if "UserController" in s.name), None)
        assert cls is not None
        assert r"MyApp\Controllers" in cls.name

    def test_extracts_call_edges(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", """<?hh
function helper(): int {
  return 42;
}

function main(): void {
  $x = helper();
}
""")
        result = analyze_hack(tmp_path)
        edge = next(
            (e for e in result.edges if "helper" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"
        assert edge.confidence == 1.0

    def test_extracts_method_call_edges(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Test.hack", """<?hh
class Test {
  private function validate(): bool {
    return true;
  }

  public function main(): void {
    $this->validate();
  }
}
""")
        result = analyze_hack(tmp_path)
        edge = next(
            (e for e in result.edges if "validate" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_extracts_static_call_edges(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Test.hack", """<?hh
class User {
  public static function find(int $id): void {}
}

class Test {
  public function main(): void {
    User::find(1);
  }
}
""")
        result = analyze_hack(tmp_path)
        edge = next(
            (e for e in result.edges if "find" in e.dst),
            None
        )
        assert edge is not None
        assert edge.edge_type == "calls"

    def test_filters_builtins(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", """<?hh
function test(): void {
  echo "hello";
  $x = strlen("test");
}
""")
        result = analyze_hack(tmp_path)
        builtin_edges = [e for e in result.edges if "echo" in e.dst or "strlen" in e.dst]
        assert len(builtin_edges) == 0

    def test_unresolved_call_target(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", """<?hh
function main(): void {
  $x = externalFunc(42);
}
""")
        result = analyze_hack(tmp_path)
        edge = next(
            (e for e in result.edges if "externalFunc" in e.dst),
            None
        )
        assert edge is not None
        assert "unresolved" in edge.dst
        assert edge.confidence == 0.6

    def test_pass_id(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", """<?hh
function add(): int { return 1; }
""")
        result = analyze_hack(tmp_path)
        func = next((s for s in result.symbols if s.name == "add"), None)
        assert func is not None
        assert func.origin == "hack.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", "<?hh\necho 'test';")
        result = analyze_hack(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "hack.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_hack(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", """<?hh
function myFunc(): void {}
""")
        result = analyze_hack(tmp_path)
        func = next((s for s in result.symbols if s.name == "myFunc"), None)
        assert func is not None
        assert func.id == func.stable_id
        assert "hack:" in func.id
        assert "test.hack" in func.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "test.hack", """<?hh
function myFunc(): void {}
""")
        result = analyze_hack(tmp_path)
        func = next((s for s in result.symbols if s.name == "myFunc"), None)
        assert func is not None
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.end_line >= func.span.start_line

    def test_recursive_calls(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Test.hack", """<?hh
class Test {
  public function factorial(int $n): int {
    if ($n <= 1) return 1;
    return $this->factorial($n - 1);
  }
}
""")
        result = analyze_hack(tmp_path)
        # factorial calls itself via $this->factorial
        edge = next(
            (e for e in result.edges if "factorial" in e.dst),
            None
        )
        assert edge is not None
        assert edge.confidence == 1.0

    def test_method_signature(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Test.hack", """<?hh
class Test {
  public function process(string $name, int $count): bool {
    return true;
  }
}
""")
        result = analyze_hack(tmp_path)
        method = next((s for s in result.symbols if "process" in s.name), None)
        assert method is not None
        assert "public function process" in method.signature
        assert ": bool" in method.signature

    def test_namespaced_function_call(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "helpers.hack", r"""<?hh
namespace MyApp\Utils;

function helper(): int {
  return 42;
}

function main(): void {
  helper();
}
""")
        result = analyze_hack(tmp_path)
        # helper() should resolve to MyApp\Utils\helper
        edge = next((e for e in result.edges if "helper" in e.dst), None)
        assert edge is not None
        assert edge.confidence == 1.0

    def test_namespaced_static_call(self, tmp_path: Path) -> None:
        make_hack_file(tmp_path, "Test.hack", r"""<?hh
namespace MyApp;

class User {
  public static function find(): void {}
}

class Test {
  public function main(): void {
    User::find();
  }
}
""")
        result = analyze_hack(tmp_path)
        # User::find should resolve within namespace
        edge = next((e for e in result.edges if "find" in e.dst), None)
        assert edge is not None

    def test_this_not_filtered_without_method(self, tmp_path: Path) -> None:
        # Just $this alone (without ->) is a builtin
        make_hack_file(tmp_path, "Test.hack", """<?hh
class Test {
  public function main(): void {
    $x = $this;
  }
}
""")
        result = analyze_hack(tmp_path)
        # $this alone should not create an edge
        this_edges = [e for e in result.edges if e.dst == "$this"]
        assert len(this_edges) == 0
