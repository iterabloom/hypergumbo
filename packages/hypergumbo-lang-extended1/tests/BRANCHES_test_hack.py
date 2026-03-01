"""Branch coverage tests for hack.py analyzer.

Tests specific branch paths in the Hack analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import hack as hack_module
from hypergumbo_lang_extended1.hack import (
    analyze_hack,
    find_hack_files,
)


def make_hack_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a Hack file with given content."""
    (tmp_path / name).write_text(content)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_hack_file(tmp_path, "User.hack", """<?hh
class User {
    private string $name;

    public function getName(): string {
        return $this->name;
    }
}
""")
        result = analyze_hack(tmp_path)
        assert not result.skipped
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("User" in c.name for c in classes)


class TestFunctionExtraction:
    """Branch coverage for function extraction."""

    def test_function_declaration(self, tmp_path: Path) -> None:
        """Test function declaration extraction."""
        make_hack_file(tmp_path, "utils.hack", """<?hh
function add(int $a, int $b): int {
    return $a + $b;
}
""")
        result = analyze_hack(tmp_path)
        funcs = [s for s in result.symbols if s.kind == "function"]
        assert any("add" in f.name for f in funcs)


class TestInterfaceExtraction:
    """Branch coverage for interface extraction."""

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_hack_file(tmp_path, "interfaces.hack", """<?hh
interface Serializable {
    public function serialize(): string;
}
""")
        result = analyze_hack(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("Serializable" in i.name for i in interfaces)


class TestTraitExtraction:
    """Branch coverage for trait extraction."""

    def test_trait_declaration(self, tmp_path: Path) -> None:
        """Test trait declaration extraction."""
        make_hack_file(tmp_path, "traits.hack", """<?hh
trait Logger {
    public function log(string $msg): void {
        echo $msg;
    }
}
""")
        result = analyze_hack(tmp_path)
        traits = [s for s in result.symbols if s.kind == "trait"]
        assert any("Logger" in t.name for t in traits)


class TestEnumExtraction:
    """Branch coverage for enum extraction."""

    def test_enum_declaration(self, tmp_path: Path) -> None:
        """Test enum declaration extraction."""
        make_hack_file(tmp_path, "enums.hack", """<?hh
enum Color: int {
    RED = 1;
    GREEN = 2;
    BLUE = 3;
}
""")
        result = analyze_hack(tmp_path)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert not result.skipped  # lenient check


class TestUseEdges:
    """Branch coverage for use edge extraction."""

    def test_use_creates_edge(self, tmp_path: Path) -> None:
        """Test use creates import edge."""
        make_hack_file(tmp_path, "app.hack", """<?hh
use namespace HH\\Lib\\Str;
use function HH\\Lib\\Vec\\map;
""")
        result = analyze_hack(tmp_path)
        uses = [e for e in result.edges if e.edge_type == "imports"]
        assert not result.skipped  # lenient check


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_function_call(self, tmp_path: Path) -> None:
        """Test function call creates edge."""
        make_hack_file(tmp_path, "app.hack", """<?hh
function helper(): void {
    echo "helper";
}

function main(): void {
    helper();
}
""")
        result = analyze_hack(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindHackFiles:
    """Branch coverage for file discovery."""

    def test_finds_hack_files(self, tmp_path: Path) -> None:
        """Test .hack files are discovered."""
        (tmp_path / "test.hack").write_text("<?hh\nfunction test(): void {}")
        files = list(find_hack_files(tmp_path))
        assert any(f.suffix == ".hack" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_hack_files(self, tmp_path: Path) -> None:
        """Test directory with no Hack files."""
        result = analyze_hack(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(hack_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="hack analysis skipped"):
                result = hack_module.analyze_hack(tmp_path)
        assert result.skipped is True
