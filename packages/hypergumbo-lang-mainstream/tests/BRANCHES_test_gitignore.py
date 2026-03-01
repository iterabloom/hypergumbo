"""Branch coverage tests for gitignore.py analyzer.

Tests specific branch paths in the Gitignore analyzer that may not be covered
by the main test suite. Focuses on:
- Helper function behavior
- Pattern extraction with metadata
- Pattern categorization
- File discovery patterns
"""
from pathlib import Path

from hypergumbo_core.analyze.base import make_symbol_id
from hypergumbo_lang_mainstream.gitignore import (
    _categorize_pattern,
    analyze_gitignore,
    find_gitignore_files,
)


def make_gitignore_file(tmp_path: Path, name: str, content: str) -> None:
    """Create a .gitignore file with given content."""
    (tmp_path / name).write_text(content)


class TestGitignoreHelperFunctions:
    """Branch coverage for helper functions."""

    def test_make_symbol_id_format(self) -> None:
        """Test symbol ID format via base class make_symbol_id."""
        symbol_id = make_symbol_id("gitignore", ".gitignore", 1, 1, "*.log", "pattern")
        assert symbol_id == "gitignore:.gitignore:1-1:*.log:pattern"


class TestPatternCategorization:
    """Branch coverage for pattern categorization."""

    def test_categorize_build_patterns(self) -> None:
        """Test build output pattern detection."""
        assert _categorize_pattern("build") == "build"
        assert _categorize_pattern("/dist/") == "build"
        assert _categorize_pattern("target") == "build"

    def test_categorize_dependency_patterns(self) -> None:
        """Test dependency pattern detection."""
        assert _categorize_pattern("node_modules") == "dependencies"
        assert _categorize_pattern("vendor") == "dependencies"
        # __pycache__ is categorized as "cache" (last category wins in PATTERN_TO_CATEGORY)
        assert _categorize_pattern("__pycache__") == "cache"

    def test_categorize_ide_patterns(self) -> None:
        """Test IDE pattern detection."""
        assert _categorize_pattern(".idea") == "ide"
        assert _categorize_pattern(".vscode") == "ide"

    def test_categorize_environment_patterns(self) -> None:
        """Test environment/secrets pattern detection."""
        assert _categorize_pattern(".env") == "environment"

    def test_categorize_log_patterns(self) -> None:
        """Test log pattern detection."""
        assert _categorize_pattern("*.log") == "logs"
        assert _categorize_pattern("logs") == "logs"

    def test_categorize_os_patterns(self) -> None:
        """Test OS file pattern detection."""
        assert _categorize_pattern(".DS_Store") == "os"
        assert _categorize_pattern("Thumbs.db") == "os"

    def test_categorize_cache_patterns(self) -> None:
        """Test cache pattern detection."""
        assert _categorize_pattern(".cache") == "cache"

    def test_categorize_test_patterns(self) -> None:
        """Test test/coverage pattern detection."""
        assert _categorize_pattern("coverage") == "test"
        assert _categorize_pattern(".coverage") == "test"
        assert _categorize_pattern(".pytest_cache") == "test"

    def test_categorize_compiled_patterns(self) -> None:
        """Test compiled file pattern detection."""
        assert _categorize_pattern("*.pyc") == "compiled"
        assert _categorize_pattern("*.o") == "compiled"
        assert _categorize_pattern("*.dll") == "compiled"

    def test_categorize_temp_patterns(self) -> None:
        """Test temp file pattern detection."""
        assert _categorize_pattern("*.swp") == "temp"
        assert _categorize_pattern("*.bak") == "temp"

    def test_categorize_unknown_pattern(self) -> None:
        """Test unknown patterns return empty category."""
        assert _categorize_pattern("some_custom_file.xyz") == ""

    def test_categorize_with_leading_slash(self) -> None:
        """Test patterns with leading slash."""
        assert _categorize_pattern("/build") == "build"

    def test_categorize_with_trailing_slash(self) -> None:
        """Test patterns with trailing slash."""
        assert _categorize_pattern("build/") == "build"


class TestPatternExtraction:
    """Branch coverage for pattern extraction."""

    def test_simple_pattern(self, tmp_path: Path) -> None:
        """Test simple pattern extraction."""
        make_gitignore_file(tmp_path, ".gitignore", """*.log
*.tmp
""")
        result = analyze_gitignore(tmp_path)
        assert not result.skipped

        patterns = [s for s in result.symbols if s.kind == "pattern"]
        assert len(patterns) >= 2

    def test_negation_pattern(self, tmp_path: Path) -> None:
        """Test negation pattern detection."""
        make_gitignore_file(tmp_path, ".gitignore", """*.log
!important.log
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        negated = [p for p in patterns if p.meta.get("is_negation")]
        assert len(negated) >= 1
        assert any("important.log" in p.name for p in negated)

    def test_directory_pattern(self, tmp_path: Path) -> None:
        """Test directory pattern detection."""
        make_gitignore_file(tmp_path, ".gitignore", """build/
dist/
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        directories = [p for p in patterns if p.meta.get("is_directory")]
        assert len(directories) >= 2

    def test_rooted_pattern(self, tmp_path: Path) -> None:
        """Test rooted pattern detection."""
        make_gitignore_file(tmp_path, ".gitignore", """/build
config.local
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        rooted = [p for p in patterns if p.meta.get("is_rooted")]
        assert len(rooted) >= 1

    def test_wildcard_pattern(self, tmp_path: Path) -> None:
        """Test wildcard pattern detection."""
        make_gitignore_file(tmp_path, ".gitignore", """*.log
build_?
[abc].txt
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        wildcards = [p for p in patterns if p.meta.get("has_wildcard")]
        assert len(wildcards) >= 3

    def test_negation_with_rooted_path(self, tmp_path: Path) -> None:
        """Test negation pattern with rooted path."""
        make_gitignore_file(tmp_path, ".gitignore", """!/important/file
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        assert len(patterns) >= 1
        # Negation with rooted path should be both negation and rooted
        pattern = patterns[0]
        assert pattern.meta.get("is_negation") is True


class TestPatternMetadata:
    """Branch coverage for pattern metadata."""

    def test_pattern_category_in_meta(self, tmp_path: Path) -> None:
        """Test pattern category is stored in metadata."""
        make_gitignore_file(tmp_path, ".gitignore", """node_modules
*.pyc
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        categories = [p.meta.get("category") for p in patterns]
        assert "dependencies" in categories
        assert "compiled" in categories

    def test_pattern_signature(self, tmp_path: Path) -> None:
        """Test pattern signature matches pattern text."""
        make_gitignore_file(tmp_path, ".gitignore", """*.log
""")
        result = analyze_gitignore(tmp_path)
        patterns = [s for s in result.symbols if s.kind == "pattern"]

        assert len(patterns) >= 1
        assert patterns[0].signature == "*.log"


class TestFindGitignoreFiles:
    """Branch coverage for file discovery."""

    def test_finds_root_gitignore(self, tmp_path: Path) -> None:
        """Test root .gitignore is discovered."""
        (tmp_path / ".gitignore").write_text("*.log")

        files = find_gitignore_files(tmp_path)
        assert len(files) >= 1
        assert any(f.name == ".gitignore" for f in files)


class TestEmptyAndMinimalFiles:
    """Branch coverage for empty/minimal file handling."""

    def test_no_gitignore_files(self, tmp_path: Path) -> None:
        """Test directory with no gitignore files."""
        result = analyze_gitignore(tmp_path)
        assert len(result.symbols) == 0

    def test_minimal_gitignore(self, tmp_path: Path) -> None:
        """Test minimal gitignore file."""
        make_gitignore_file(tmp_path, ".gitignore", """*.log
""")
        result = analyze_gitignore(tmp_path)
        assert not result.skipped


class TestAnalysisRun:
    """Branch coverage for analysis run metadata."""

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        """Test analysis run metadata is populated."""
        make_gitignore_file(tmp_path, ".gitignore", """node_modules
*.pyc
""")
        result = analyze_gitignore(tmp_path)
        assert result.run is not None
        assert result.run.files_analyzed >= 1


class TestComplexGitignore:
    """Branch coverage for complex gitignore files."""

    def test_comprehensive_gitignore(self, tmp_path: Path) -> None:
        """Test comprehensive .gitignore with all pattern types."""
        make_gitignore_file(tmp_path, ".gitignore", """# Build outputs
/build
/dist/
target

# Dependencies
node_modules
vendor
__pycache__

# IDE
.idea
.vscode

# Environment
.env
.env.local

# Logs
*.log
logs/

# Cache
.cache
*.cache

# Coverage
coverage
.coverage
htmlcov

# Compiled
*.pyc
*.o
*.so

# Temp
*.swp
*.bak
*~

# Exceptions
!.gitkeep
""")
        result = analyze_gitignore(tmp_path)

        # Should have many patterns
        patterns = [s for s in result.symbols if s.kind == "pattern"]
        assert len(patterns) >= 20

        # Should have various metadata flags
        assert any(p.meta.get("is_negation") for p in patterns)
        assert any(p.meta.get("is_directory") for p in patterns)
        assert any(p.meta.get("is_rooted") for p in patterns)
        assert any(p.meta.get("has_wildcard") for p in patterns)
