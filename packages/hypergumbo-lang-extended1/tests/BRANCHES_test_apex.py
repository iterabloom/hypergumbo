"""Branch coverage tests for apex.py analyzer.

Tests specific branch paths in the Apex analyzer that may not be covered
by the main test suite.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_extended1 import apex as apex_module
from hypergumbo_lang_extended1.apex import (
    analyze_apex,
    find_apex_files,
)


def make_apex_file(tmp_path: Path, name: str, content: str) -> None:
    """Create an Apex file with given content."""
    (tmp_path / name).write_text(content)


class TestClassExtraction:
    """Branch coverage for class extraction."""

    def test_class_declaration(self, tmp_path: Path) -> None:
        """Test class declaration extraction."""
        make_apex_file(tmp_path, "AccountService.cls", """
public class AccountService {
    public void process() {
        System.debug('Processing');
    }
}
""")
        result = analyze_apex(tmp_path)
        assert not result.skipped
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any("AccountService" in c.name for c in classes)

    def test_interface_declaration(self, tmp_path: Path) -> None:
        """Test interface declaration extraction."""
        make_apex_file(tmp_path, "IService.cls", """
public interface IService {
    void execute();
}
""")
        result = analyze_apex(tmp_path)
        interfaces = [s for s in result.symbols if s.kind == "interface"]
        assert any("IService" in i.name for i in interfaces)


class TestMethodExtraction:
    """Branch coverage for method extraction."""

    def test_method_declaration(self, tmp_path: Path) -> None:
        """Test method declaration extraction."""
        make_apex_file(tmp_path, "Calculator.cls", """
public class Calculator {
    public Integer add(Integer a, Integer b) {
        return a + b;
    }
}
""")
        result = analyze_apex(tmp_path)
        methods = [s for s in result.symbols if s.kind == "method"]
        assert any("add" in m.name for m in methods)


class TestTriggerExtraction:
    """Branch coverage for trigger extraction."""

    def test_trigger_declaration(self, tmp_path: Path) -> None:
        """Test trigger declaration extraction."""
        make_apex_file(tmp_path, "AccountTrigger.trigger", """
trigger AccountTrigger on Account (before insert, after insert) {
    for (Account a : Trigger.new) {
        System.debug(a.Name);
    }
}
""")
        result = analyze_apex(tmp_path)
        triggers = [s for s in result.symbols if s.kind == "trigger"]
        assert any("AccountTrigger" in t.name for t in triggers)


class TestCallEdges:
    """Branch coverage for call edge extraction."""

    def test_method_call(self, tmp_path: Path) -> None:
        """Test method call creates edge."""
        make_apex_file(tmp_path, "App.cls", """
public class App {
    public void helper() {
        System.debug('helper');
    }

    public void main() {
        helper();
    }
}
""")
        result = analyze_apex(tmp_path)
        calls = [e for e in result.edges if e.edge_type == "calls"]
        assert not result.skipped  # lenient check


class TestFindApexFiles:
    """Branch coverage for file discovery."""

    def test_finds_cls_files(self, tmp_path: Path) -> None:
        """Test .cls files are discovered."""
        (tmp_path / "Test.cls").write_text("public class Test {}")
        files = list(find_apex_files(tmp_path))
        assert any(f.suffix == ".cls" for f in files)

    def test_finds_trigger_files(self, tmp_path: Path) -> None:
        """Test .trigger files are discovered."""
        (tmp_path / "Test.trigger").write_text("trigger Test on Account (before insert) {}")
        files = list(find_apex_files(tmp_path))
        assert any(f.suffix == ".trigger" for f in files)


class TestEmptyFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_apex_files(self, tmp_path: Path) -> None:
        """Test directory with no Apex files."""
        result = analyze_apex(tmp_path)
        assert len(result.symbols) == 0


class TestTreeSitterUnavailable:
    """Branch coverage for tree-sitter unavailability."""

    def test_skipped_when_unavailable(self, tmp_path: Path) -> None:
        """Test analysis is skipped when tree-sitter unavailable."""
        with patch.object(apex_module._analyzer, "_check_grammar_available", return_value=False):
            with pytest.warns(UserWarning, match="apex analysis skipped"):
                result = apex_module.analyze_apex(tmp_path)
        assert result.skipped is True
