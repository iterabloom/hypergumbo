"""Tests for the gitignore file analyzer."""

from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_lang_mainstream import gitignore as gitignore_module
from hypergumbo_lang_mainstream.gitignore import (
    GitignoreAnalysisResult,
    analyze_gitignore,
    find_gitignore_files,
    is_gitignore_tree_sitter_available,
)


def make_gitignore_file(tmp_path: Path, name: str, content: str) -> Path:
    """Create a gitignore file in the temp directory."""
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


class TestFindGitignoreFiles:
    """Tests for find_gitignore_files function."""

    def test_finds_root_gitignore(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        files = find_gitignore_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == ".gitignore"

    def test_finds_nested_gitignore(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        make_gitignore_file(tmp_path, "subdir/.gitignore", "*.tmp\n")
        files = find_gitignore_files(tmp_path)
        assert len(files) == 2

    def test_empty_directory(self, tmp_path: Path) -> None:
        files = find_gitignore_files(tmp_path)
        assert files == []


class TestIsGitignoreTreeSitterAvailable:
    """Tests for is_gitignore_tree_sitter_available function."""

    def test_returns_true_when_available(self) -> None:
        result = is_gitignore_tree_sitter_available()
        assert result is True

    def test_returns_false_when_unavailable(self) -> None:
        with patch.object(gitignore_module, "is_gitignore_tree_sitter_available", return_value=False):
            assert gitignore_module.is_gitignore_tree_sitter_available() is False


class TestAnalyzeGitignore:
    """Tests for analyze_gitignore function."""

    def test_skips_when_unavailable(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        with patch.object(gitignore_module, "is_gitignore_tree_sitter_available", return_value=False):
            with pytest.warns(UserWarning, match="Gitignore analysis skipped"):
                result = gitignore_module.analyze_gitignore(tmp_path)
        assert result.skipped is True
        assert "not available" in result.skip_reason

    def test_extracts_simple_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        assert not result.skipped
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.name == "*.log"
        assert pattern.language == "gitignore"

    def test_extracts_multiple_patterns(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", """
*.log
build/
node_modules/
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]
        assert len(patterns) == 3
        names = {p.name for p in patterns}
        assert "*.log" in names
        assert "build/" in names
        assert "node_modules/" in names

    def test_detects_directory_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "build/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_directory") is True

    def test_detects_non_directory_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_directory") is False

    def test_detects_rooted_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "/build/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_rooted") is True

    def test_detects_non_rooted_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "build/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_rooted") is False

    def test_detects_negation_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "!important.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_negation") is True

    def test_detects_non_negation_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_negation") is False

    def test_detects_wildcard_star(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("has_wildcard") is True

    def test_detects_wildcard_question(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "file?.txt\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("has_wildcard") is True

    def test_detects_wildcard_bracket(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "file[0-9].txt\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("has_wildcard") is True

    def test_detects_no_wildcard(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "Makefile\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("has_wildcard") is False

    def test_categorizes_build_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "build/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "build"

    def test_categorizes_dist_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "dist/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "build"

    def test_categorizes_node_modules_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "node_modules/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "dependencies"

    def test_categorizes_venv_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", ".venv/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "dependencies"

    def test_categorizes_ide_pattern_idea(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", ".idea/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "ide"

    def test_categorizes_ide_pattern_vscode(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", ".vscode/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "ide"

    def test_categorizes_env_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", ".env\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "environment"

    def test_categorizes_log_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "logs"

    def test_categorizes_os_pattern_ds_store(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", ".DS_Store\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "os"

    def test_categorizes_cache_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "__pycache__/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        # __pycache__ is in both dependencies and cache, dependencies takes precedence
        assert pattern.meta.get("category") in ("dependencies", "cache")

    def test_categorizes_test_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "coverage/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "test"

    def test_categorizes_compiled_pattern_pyc(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.pyc\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "compiled"

    def test_categorizes_compiled_pattern_class(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.class\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "compiled"

    def test_categorizes_temp_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.swp\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "temp"

    def test_uncategorized_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "custom-file.txt\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == ""

    def test_pass_id(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.origin == "gitignore.tree_sitter"

    def test_analysis_run_metadata(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        assert result.run is not None
        assert result.run.pass_id == "gitignore.tree_sitter"
        assert result.run.execution_id.startswith("uuid:")
        assert result.run.duration_ms >= 0

    def test_empty_repo(self, tmp_path: Path) -> None:
        result = analyze_gitignore(tmp_path)
        assert result.symbols == []
        assert result.edges == []
        assert result.run is None

    def test_stable_ids(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.id == pattern.stable_id
        assert "gitignore:" in pattern.id

    def test_span_info(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.span is not None
        assert pattern.span.start_line >= 1

    def test_multiple_files(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        make_gitignore_file(tmp_path, "subdir/.gitignore", "*.tmp\n")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]
        assert len(patterns) == 2

    def test_run_files_analyzed(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "*.log\n")
        make_gitignore_file(tmp_path, "a/.gitignore", "*.tmp\n")
        make_gitignore_file(tmp_path, "b/.gitignore", "*.bak\n")
        result = analyze_gitignore(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed == 3

    def test_handles_comments(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", """
# Build outputs
build/
# Editor files
*.swp
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]
        assert len(patterns) == 2
        names = {p.name for p in patterns}
        assert "build/" in names
        assert "*.swp" in names

    def test_complete_gitignore(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", """
# Build
/build/
/dist/

# Dependencies
node_modules/
.venv/

# IDE
.idea/
.vscode/

# Environment
.env

# Logs
*.log

# OS
.DS_Store

# Compiled
*.pyc

# Cache
__pycache__/

# Keep important.log
!important.log
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]
        assert len(patterns) >= 12

        # Check categories are diverse
        categories = {p.meta.get("category") for p in patterns}
        assert "build" in categories
        assert "dependencies" in categories
        assert "ide" in categories

        # Check negation is detected
        negation = next((p for p in patterns if p.meta.get("is_negation")), None)
        assert negation is not None
        assert negation.name == "!important.log"

    def test_rooted_negation_pattern(self, tmp_path: Path) -> None:
        make_gitignore_file(tmp_path, ".gitignore", "!/important/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("is_negation") is True
        assert pattern.meta.get("is_rooted") is True

    def test_categorizes_substring_match(self, tmp_path: Path) -> None:
        """Test that patterns containing known substrings get categorized."""
        # "project_dist" contains "dist" which is in the build category
        make_gitignore_file(tmp_path, ".gitignore", "project_dist/\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "build"

    def test_categorizes_extension_match(self, tmp_path: Path) -> None:
        """Test that patterns with known extensions get categorized."""
        # "file.bak" ends with ".bak" which matches the *.bak pattern in temp category
        # Using .bak because it won't match any non-wildcard substring patterns
        make_gitignore_file(tmp_path, ".gitignore", "file.bak\n")
        result = analyze_gitignore(tmp_path)
        pattern = next((s for s in result.symbols if s.kind == "pattern"), None)
        assert pattern is not None
        assert pattern.meta.get("category") == "temp"
